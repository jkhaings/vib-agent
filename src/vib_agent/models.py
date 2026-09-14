"""Pydantic models for all structured data crossing pdm_core boundaries.

Field names and validation rules for MachineMeta are ported verbatim from the
Machine Registry Editor in reference/flows.json (its inline JS validation is
the source of truth for what makes a machine record valid).

PeakSet is the Layer-5 architectural boundary (see bearing_rca.py): every
detector consumes a PeakSet and never a raw source format. NCD packet
triplets and spectrum-derived peaks both normalize into PeakSet via a
peaks_from_*() adapter — this is where a future third-party data source
(e.g. route-collector exports) would plug in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Axis = Literal["x", "y", "z"]
IsoZone = Literal["A", "B", "C", "D"]
IsoSeverity = Literal["ok", "info", "warn", "danger"]
Confidence = Literal["high", "medium", "low"]
CheckStatus = Literal["pass", "warn", "fail", "not_applicable"]
ThresholdSource = Literal["custom", "factory_default", "iso_20816_3"]

# Session LIMITS-1a — which authority the zone letter was computed against.
# NOT the same question as ThresholdSource, which names WHICH tier won: this
# collapses that to the only distinction a reader of the report cares about,
# "the ISO table" vs "a limit somebody set". `factory_default` maps to "custom"
# for that reason — a factory-wide default is a limit a human chose, not a row
# of ISO 20816-3. Deliberately NOT called `zone_source`: that name is taken and
# means something else entirely (readings.zone_source in {computed, imported},
# Alembic 0003_account2_zone_source — data provenance, not zone authority).
ZoneBasis = Literal["iso", "custom"]

# Session GEOM-A — how the motor is fed. Captured for the coverage roster and the
# analysis-parameters table; no detector reads it yet (VFD carrier artifacts and
# 2xLF stay not-assessed until SIDEBAND). "soft_starter" is a starter, not a
# continuous drive: it matters because it is NOT a VFD, and saying so is the
# point of the field.
DriveType = Literal["direct_on_line", "vfd", "soft_starter"]

# Session B (B1) — spectrum kind. ISO 20816 severity is separate; this is which
# DSP produced a spectrum, so detectors read the right one: bearing detectors read
# an envelope (demodulated) spectrum; 1x-family detectors (imbalance/misalignment/
# looseness/belt/blade-pass/resonance) read a raw or velocity spectrum, NEVER the
# envelope (its 1x line is a demodulation artifact). The NCD-triplet path is its
# own PeakSet kind ("ncd_peaks"). Informational: routing is by WHICH spectrum feeds
# a detector's context, never by a detector branching on this value.
SpectrumKind = Literal["raw_acceleration", "velocity", "envelope"]

# Session A — severity truthfulness. ISO 20816 severity is a VELOCITY judgement.
# When velocity is absent (acceleration-only reading) or present-but-implausible,
# severity cannot be assessed: the zone becomes "not_assessable" and the finding
# severity "unrated" — never a velocity coerced to 0.0 mm/s that reads as Zone A /
# "ok". Kept as widened aliases (not extra enum members) so every existing
# A/B/C/D and ok/info/warn/danger consumer stays exhaustive on the assessable case.
IsoZoneOrNA = IsoZone | Literal["not_assessable"]
IsoSeverityOrUnrated = IsoSeverity | Literal["unrated"]


# ─────────────────────────────────────────────────────────────────────────
# Machine registry (MachineMeta + sub-models)
# ─────────────────────────────────────────────────────────────────────────


class MachineThresholds(BaseModel):
    """Machine-level ISO zone override. All three values required together."""

    ab: float
    bc: float
    cd: float
    source_note: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> MachineThresholds:
        for k in ("ab", "bc", "cd"):
            if getattr(self, k) <= 0:
                raise ValueError(f"thresholds.{k} must be > 0")
        if not (self.ab < self.bc < self.cd):
            raise ValueError(
                f"thresholds must satisfy ab < bc < cd (got {self.ab}, {self.bc}, {self.cd})"
            )
        return self


class BearingSpec(BaseModel):
    """Bearing geometry for fault-frequency computation. All 4 core fields required together."""

    n_balls: int
    ball_dia_mm: float
    pitch_dia_mm: float
    contact_angle_deg: float = 0.0
    model: str | None = None

    @field_validator("n_balls")
    @classmethod
    def _n_balls_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("bearing.n_balls must be a positive integer")
        return v

    @field_validator("ball_dia_mm", "pitch_dia_mm")
    @classmethod
    def _dims_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("bearing dimensions must be > 0")
        return v

    @field_validator("contact_angle_deg")
    @classmethod
    def _angle_range(cls, v: float) -> float:
        if not (0 <= v <= 90):
            raise ValueError("bearing.contact_angle_deg must be 0-90")
        return v

    @model_validator(mode="after")
    def _ball_lt_pitch(self) -> BearingSpec:
        if self.ball_dia_mm >= self.pitch_dia_mm:
            raise ValueError("bearing.ball_dia_mm must be < pitch_dia_mm")
        return self


class BeltSpec(BaseModel):
    """The belt fundamental, plus the drive geometry it was derived from.

    `freq_hz` is the only field `pdm_core.bearing_rca.detect_belt_fault` has
    ever read and the only one it reads now — the four fields below are
    PROVENANCE, recorded so the report can print the derivation instead of
    asserting a number out of nowhere. They arrive together or not at all:
    Session GEOM-A computes `freq_hz` from them in the adapter layer
    (`adapters/uploads/common.py::belt_spec_from_form`, the `envelope_spectrum`
    precedent — derivation lives in adapters), and a Case JSON may still supply
    `freq_hz` alone, exactly as before this session.
    """

    freq_hz: float
    #: Pulley on the shaft whose running speed was entered, in mm.
    drive_pulley_mm: float | None = None
    driven_pulley_mm: float | None = None
    #: Shaft-to-shaft centre distance, in mm.
    center_distance_mm: float | None = None
    #: Theoretical wrap length from the three dimensions above, in mm.
    belt_length_mm: float | None = None

    @field_validator("freq_hz")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("belt.freq_hz must be > 0")
        return v

    @field_validator("drive_pulley_mm", "driven_pulley_mm", "center_distance_mm",
                     "belt_length_mm")
    @classmethod
    def _dims_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("belt geometry dimensions must be > 0")
        return v

    @model_validator(mode="after")
    def _geometry_is_all_or_nothing(self) -> BeltSpec:
        geometry = (self.drive_pulley_mm, self.driven_pulley_mm, self.center_distance_mm)
        if any(v is not None for v in geometry) and not all(v is not None for v in geometry):
            raise ValueError(
                "belt geometry needs ALL of drive_pulley_mm, driven_pulley_mm and "
                "center_distance_mm, or none of them"
            )
        if all(v is not None for v in geometry):
            # Two pulleys on a common belt cannot overlap: the centre distance
            # must clear both radii. Without this an impossible layout produces
            # a belt "length" shorter than the wrap and a fabricated frequency.
            assert self.drive_pulley_mm is not None and self.driven_pulley_mm is not None
            assert self.center_distance_mm is not None
            min_centres = (self.drive_pulley_mm + self.driven_pulley_mm) / 2.0
            if self.center_distance_mm <= min_centres:
                raise ValueError(
                    f"belt centre distance ({self.center_distance_mm:g} mm) must exceed the sum "
                    f"of the pulley radii ({min_centres:g} mm) — the pulleys would overlap"
                )
        return self


class MachineMeta(BaseModel):
    """A machine registry entry. Field set and validation ported from the
    Machine Registry Editor's inline JS in reference/flows.json.
    """

    mac: str
    name: str
    active: bool
    location: str | None = None
    type: str | None = None  # machine_type, e.g. "pump", "motor"

    iso_group: Literal["1", "2"] | None = None
    iso_support: Literal["rigid", "flexible"] | None = None
    thresholds: MachineThresholds | None = None

    min_running_g: float = 0.010
    rpm_nominal: float | None = None
    rpm_tolerance_pct: float = 10.0
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    min_battery_pct: float | None = None

    min_readings: int = 30
    zscore_watch: float = 2.5
    zscore_flag: float = 3.5

    trend_eligible: bool = True
    coupled: bool = True
    # Session GEOM-A. `coupled` is a bool with a default, so on its own it cannot
    # tell "the analyst declared this machine coupled" from "nobody said". The
    # bent-shaft branch only cares about False, so pdm_core is unaffected either
    # way -- but the coverage roster prints a different REASON for the two, and a
    # report that says "not provided" about an answer the analyst gave is false.
    coupled_stated: bool = False
    axial_axis: Axis = "x"

    bearing: BearingSpec | None = None
    belt: BeltSpec | None = None
    blades: int | None = None

    # ── Session GEOM-A — machine geometry captured on the upload form ──────
    # Every field here is optional, defaults to absent, and changes nothing
    # about an analysis that does not supply it. `blades` (above) and `belt`
    # (above) already had detectors and were simply unreachable from the
    # product; the four below have NO detector yet and are captured so the
    # geometry is on file when SIDEBAND arrives -- the coverage roster says
    # exactly that, and the analysis-parameters table prints them. They are
    # read by report/ only; pdm_core does not know they exist.
    gear_teeth_driving: int | None = None
    gear_teeth_driven: int | None = None
    rotor_bars: int | None = None
    poles: int | None = None
    line_freq_hz: float | None = None
    drive_type: DriveType | None = None

    @field_validator("min_running_g")
    @classmethod
    def _min_running_g_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("min_running_g must be > 0")
        return v

    @field_validator("rpm_nominal")
    @classmethod
    def _rpm_nominal_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("rpm_nominal must be > 0")
        return v

    @field_validator("min_battery_pct")
    @classmethod
    def _battery_range(cls, v: float | None) -> float | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("min_battery_pct must be 0-100")
        return v

    @field_validator("min_readings")
    @classmethod
    def _min_readings_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("min_readings must be a positive integer")
        return v

    @field_validator("blades")
    @classmethod
    def _blades_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("blades must be a positive integer")
        return v

    @field_validator("gear_teeth_driving", "gear_teeth_driven", "rotor_bars")
    @classmethod
    def _counts_positive(cls, v: int | None, info: Any) -> int | None:
        if v is not None and v <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return v

    @field_validator("poles")
    @classmethod
    def _poles_even(cls, v: int | None) -> int | None:
        # An induction or synchronous machine is wound in pole PAIRS, so an odd
        # pole count does not exist. Recording one would poison the pole-pass
        # frequency SIDEBAND will compute from it, silently.
        if v is not None and (v <= 0 or v % 2 != 0):
            raise ValueError("poles must be a positive even integer (2, 4, 6, ...)")
        return v

    @field_validator("line_freq_hz")
    @classmethod
    def _line_freq_positive(cls, v: float | None) -> float | None:
        # Not constrained to 50/60: on a VFD the stator sees the drive's output
        # frequency, which is whatever the process asked for.
        if v is not None and v <= 0:
            raise ValueError("line_freq_hz must be > 0")
        return v

    @model_validator(mode="after")
    def _cross_field_checks(self) -> MachineMeta:
        if (self.temp_min_c is None) != (self.temp_max_c is None):
            raise ValueError("temp gating needs BOTH temp_min_c and temp_max_c")
        if (
            self.temp_min_c is not None
            and self.temp_max_c is not None
            and self.temp_min_c >= self.temp_max_c
        ):
            raise ValueError("temp_min_c must be < temp_max_c")
        if self.zscore_watch >= self.zscore_flag:
            raise ValueError("zscore_watch must be < zscore_flag")
        return self


# ─────────────────────────────────────────────────────────────────────────
# Sensor input
# ─────────────────────────────────────────────────────────────────────────


class SensorData(BaseModel):
    """NCD Gen4 packet `sensor_data` payload. Field names match reference/flows.json
    'Normalize NCD → PdM schema' node's documented shape.
    """

    mode: int | None = None
    msg_type: str = "regular"
    odr: str | None = None
    temperature: float | None = None
    rpm: float | None = None

    x_velocity_mm_sec: float | None = None
    y_velocity_mm_sec: float | None = None
    z_velocity_mm_sec: float | None = None

    x_rms_ACC_G: float | None = None
    y_rms_ACC_G: float | None = None
    z_rms_ACC_G: float | None = None

    x_max_ACC_G: float | None = None
    y_max_ACC_G: float | None = None
    z_max_ACC_G: float | None = None

    x_displacement_mm: float | None = None
    y_displacement_mm: float | None = None
    z_displacement_mm: float | None = None

    x_peak_one_Hz: float | None = None
    x_peak_two_Hz: float | None = None
    x_peak_three_Hz: float | None = None
    y_peak_one_Hz: float | None = None
    y_peak_two_Hz: float | None = None
    y_peak_three_Hz: float | None = None
    z_peak_one_Hz: float | None = None
    z_peak_two_Hz: float | None = None
    z_peak_three_Hz: float | None = None


class Spectrum(BaseModel):
    """Optional FFT amplitude spectrum — extra evidence beyond the NCD peak triplets.
    Not present in the reference pipeline; synth-generated and used to derive
    additional peaks (peaks_from_spectrum) and sidebands.
    """

    freq_hz: list[float]
    amplitude: list[float]
    fmax_hz: float | None = None
    # Session B (B1): which DSP produced this spectrum. Defaults to "envelope" so
    # the 62 committed CWRU/MFPT case JSONs (which omit the field) still validate.
    kind: SpectrumKind = "envelope"


# ─────────────────────────────────────────────────────────────────────────
# Layer 1 — ISO classification output
# ─────────────────────────────────────────────────────────────────────────


class ResolvedThresholds(BaseModel):
    """Output of resolve_thresholds(): the 3-tier priority result (machine
    override > factory default > ISO table), bundled with its provenance.
    """

    thresholds: MachineThresholds
    source: ThresholdSource
    note: str

    # Session LIMITS-1a — what tier 3 says for this machine, resolved REGARDLESS
    # of which tier actually won, so a custom-limit reading can still report the
    # zone ISO would have given. Tier 1 short-circuits before the ISO table is
    # ever consulted (:43-48), and resolve_thresholds is the only place still
    # holding `iso_table` at that moment; computing it here is what lets
    # classify() fill `Reading.iso_zone_would_be` without a new argument, and
    # therefore what leaves pipeline.py untouched. None when the ISO table
    # cannot answer for this machine (no iso_group/iso_support, or a key the
    # table does not carry) — which is not an error: a custom limit exists
    # precisely so a machine ISO 20816-3 does not cover can still be judged.
    iso_fallback: MachineThresholds | None = None


class Reading(BaseModel):
    """Layer 1 output. Field names match reference/flows.json Layer 1's msg.reading."""

    ts: str
    factory_id: str | None = None
    factory_timezone: str | None = None
    machine_id: str
    machine_type: str = "unknown"
    mac: str

    # None on any axis whose velocity was not reported; all-None => not_assessable
    # (missing velocity is NEVER coerced to 0.0 — that would fake a Zone A reading).
    x_vel_mms: float | None
    y_vel_mms: float | None
    z_vel_mms: float | None
    severity_rms: float | None  # None when velocity absent (severity not assessable)
    dominant_axis: Axis | None  # None when severity_rms is None

    iso_zone: IsoZoneOrNA
    iso_severity: IsoSeverityOrUnrated
    iso_group: str | None = None
    iso_support: str | None = None
    threshold_source: ThresholdSource
    threshold_note: str
    th_ab: float
    th_bc: float
    th_cd: float

    margin_to_next_boundary: float | None = None  # additive, not in reference
    # Session A: why severity could not be assessed ("velocity not measured" /
    # "implausible velocity units"); None on a normally-classified reading.
    not_assessable_reason: str | None = None

    # Session LIMITS-1a — the two fields that let a report print BOTH zones when
    # a machine is judged against a plant limit instead of the ISO table.
    #
    # `zone_basis` says which authority the letter above came from; the
    # boundaries it came from are already carried, as th_ab/th_bc/th_cd.
    # `iso_zone_would_be` is the letter ISO 20816-3 WOULD have given the same
    # severity_rms — set only when zone_basis == "custom" (on the ISO path there
    # is no "would be", it IS the zone) and only when the ISO table can answer
    # for this machine; None otherwise.
    #
    # Both default, and that is load-bearing rather than convenience: readings
    # are constructed by hand in tests outside this session's scope, and a
    # required field would have forced edits there. The defaults are also the
    # ISO path's own values, so nothing on that path moves.
    zone_basis: ZoneBasis = "iso"
    iso_zone_would_be: IsoZone | None = None


# ─────────────────────────────────────────────────────────────────────────
# Layer 5 — normalized peak boundary (Amendment A3)
# ─────────────────────────────────────────────────────────────────────────


class Peak(BaseModel):
    axis: Axis
    freq: float
    rank: int
    amplitude: float | None = None


class PeakSet(BaseModel):
    """The single normalized input every Layer 5 detector consumes.

    Two adapters produce this today: peaks_from_ncd() (NCD packet triplets —
    the reference pipeline's native shape) and peaks_from_spectrum() (scipy
    find_peaks over a synth-generated FFT array). A future data source (e.g.
    a route-collector export) adds a third adapter; detectors never change.
    """

    source: Literal["ncd_triplet", "spectrum"]
    shaft_freq_hz: float
    rpm: float
    peaks: list[Peak]
    velocities_mms: dict[str, float]
    # Session B (B1): the kind of the spectrum these peaks came from (or
    # "ncd_peaks" for the NCD-triplet path). INFORMATIONAL — for debugging/
    # provenance only; no detector branches on it (that would be a hidden
    # constant). Routing is done by which PeakSet builds a detector's context.
    kind: Literal["ncd_peaks", "raw_acceleration", "velocity", "envelope"] = "ncd_peaks"
    # Session B (B4): per-axis MEAN spectral amplitude, the amplitude-floor
    # reference (peak-to-mean ratio). Set only by peaks_from_spectrum (a full
    # spectrum was captured); None for the NCD triplet path, which carries no
    # broadband floor — so the floor check is inert there (byte-identical).
    axis_mean_amp: dict[str, float] | None = None
    # Session PDMFIX: which axes were ACTUALLY MEASURED, as distinct from which
    # appear in `velocities_mms` — the pipeline coerces every absent axis to 0.0
    # there, so that dict cannot tell a silent channel from a missing one. An
    # absent channel must never satisfy a detector conjunct ("axial quiet") nor
    # raise confidence. None = UNKNOWN, in which case every consumer falls back
    # to the pre-PDMFIX assumption that all three axes were measured, so a
    # hand-built PeakSet stays byte-identical.
    measured_axes: list[Axis] | None = None


# ─────────────────────────────────────────────────────────────────────────
# Quality gate
# ─────────────────────────────────────────────────────────────────────────


class LifecycleState(BaseModel):
    """Caller-supplied sensor lifecycle state (ported from the reference's
    'Lifecycle Tracker' node, simplified to a pure input — no mutation
    happens inside quality_gate; the caller owns advancing this between
    readings, the same way WelfordState is threaded through anomaly.py).
    """

    configuring: bool = False
    startup_counter: int = 999  # small value = within the post-start transient window
    last_start_ts_ms: float | None = None


class Check(BaseModel):
    name: str
    status: CheckStatus
    reason: str | None = None


class QualityGateResult(BaseModel):
    overall: Literal["pass", "warn", "fail"]
    checks: list[Check]
    train_baseline: bool
    train_reasons: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Layer 2 — Z-score
# ─────────────────────────────────────────────────────────────────────────


class WelfordAxisState(BaseModel):
    sum_w: float = 0.0
    mean: float = 0.0
    m2: float = 0.0
    last_ts_ms: float


class WelfordState(BaseModel):
    x: WelfordAxisState
    y: WelfordAxisState
    z: WelfordAxisState


class AxisZScore(BaseModel):
    value: float
    mean: float
    std: float
    z: float
    n: float
    status: Literal["invalid", "warming_up", "normal", "watch", "anomaly"]
    flagged: bool


class ZScoreResult(BaseModel):
    mac: str
    machine_id: str
    axes: dict[str, AxisZScore]
    any_flag: bool
    max_z: float
    flagged_axes: list[str]
    window_cap: int
    min_readings: int
    trained: bool


# ─────────────────────────────────────────────────────────────────────────
# Layer 3 — Isolation Forest
# ─────────────────────────────────────────────────────────────────────────


class IsolationForestResult(BaseModel):
    status: Literal["ok", "not_enough_history"]
    score: float | None = None
    anomaly: bool | None = None
    n_samples: int = 0


# ─────────────────────────────────────────────────────────────────────────
# Layer 4 — Trend
# ─────────────────────────────────────────────────────────────────────────


class HistoryPoint(BaseModel):
    ts: datetime
    value: float


class TrendResult(BaseModel):
    status: Literal["insufficient_data", "ok"]
    severity: IsoSeverity
    n_days: int
    slope: float
    pct_change: float
    baseline_avg: float | None = None
    baseline_std: float | None = None
    current_avg: float | None = None
    alarm_limit: float | None = None

    # additive, not in reference:
    r_squared: float | None = None
    days_to_next_boundary: float | None = None
    trend_note: str | None = None


# ─────────────────────────────────────────────────────────────────────────
# Layer 5 — RCA
# ─────────────────────────────────────────────────────────────────────────


class BearingFreqs(BaseModel):
    BPFO: float
    BPFI: float
    BSF: float
    FTF: float


class ConfidenceFactor(BaseModel):
    """One signed contributor to a fault's computed confidence. `delta` is the
    weighted score contribution (from config/thresholds.json `confidence`);
    `detail` is a human-readable evidence bullet for the report.
    """

    name: str
    detail: str
    delta: float


class FaultMatch(BaseModel):
    fault: str
    description: str
    freq_hz: float | None = None
    expected_hz: float | None = None
    axis: Axis
    confidence: Confidence
    confidence_evidence: list[ConfidenceFactor] = Field(default_factory=list)
    evidence: str

    harmonic_present: bool | None = None
    bearing_model: str | None = None
    harmonics_by_axis: dict[str, list[str]] | None = None
    loudest_axis: str | None = None
    axial_velocity_mms: float | None = None
    radial_velocity_mms: float | None = None
    axial_radial_ratio: float | None = None

    sidebands: list[float] | None = None  # additive, only when spectrum evidence present


class DifferentialCandidate(BaseModel):
    """A fault the evidence raised but the adjudication rules did NOT commit to
    — either suppressed by a stronger diagnosis or downgraded by a competing
    one. `adjudication` states exactly why it was set aside; this is the
    interaction-rule reasoning made visible ("Also considered" in the report).
    """

    fault: str
    description: str
    confidence: Confidence
    adjudication: str


class RcaResult(BaseModel):
    status: Literal["ok", "machine_off", "skipped", "error"]
    shaft_freq_hz: float = 0.0
    rpm: float = 0.0
    axial_axis: Axis | None = None
    bearing_specs_present: bool = False
    bearing_freqs: BearingFreqs | None = None
    axial_radial_ratio: float | None = None
    primary_findings: list[FaultMatch] = Field(default_factory=list)
    differential: list[DifferentialCandidate] = Field(default_factory=list)
    reason: str | None = None


class RecommendedMeasurement(BaseModel):
    """A follow-up measurement that would resolve an unresolved ambiguity or a
    data-quality problem. Emitted by pdm_core/recommendations.py — the
    "system knows what it doesn't know and says what to collect next" pillar.
    """

    technique: str
    purpose: str
    trigger: str
    priority: Literal["high", "medium", "low"]


# ─────────────────────────────────────────────────────────────────────────
# Agent-facing output contract
# ─────────────────────────────────────────────────────────────────────────


class Finding(BaseModel):
    fault: str
    severity: IsoSeverityOrUnrated  # "unrated" when the reading's zone is not_assessable
    confidence: Confidence
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    machine_id: str
    mac: str
    ts: str
    quality_gate: QualityGateResult
    iso: Reading | None = None
    zscore: ZScoreResult | None = None
    isolation_forest: IsolationForestResult | None = None
    trend: TrendResult | None = None
    rca: RcaResult | None = None
    findings: list[Finding] = Field(default_factory=list)
    recommended_measurements: list[RecommendedMeasurement] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Synthetic / eval case contract (Phase 2)
# ─────────────────────────────────────────────────────────────────────────


class CaseExpected(BaseModel):
    """Expected-outcome block for a synthetic or eval case."""

    zone: IsoZoneOrNA | None = None
    faults: list[str] = Field(default_factory=list)
    insufficient: bool = False
    gate_overall: Literal["pass", "warn", "fail"] | None = None

    # Benchmark metadata (Phase 3.5 — CWRU), additive: table columns for
    # outputs/cwru_results.md. Unused by synthetic/streaming cases.
    fault_type: str | None = None
    diameter_in: float | None = None

    # MFPT real-world scoring metadata (Phase 7), additive. Each real-world
    # file's OWN embedded fault-frequency orders (dimensionless, multiples
    # of shaft rate) — NOT an invented answer key (faults stays [] for
    # these cases); carried here purely so eval/runner.py::run_mfpt_suite
    # can score a finding's matched frequency against the file's own stated
    # values per Part B's two-outcome rule. None for every non-MFPT case
    # and for MFPT rig cases (which have a real fault_type/faults answer).
    embedded_ball_order: float | None = None
    embedded_cage_order: float | None = None
    embedded_outer_order: float | None = None
    embedded_inner_order: float | None = None
    load_lbs: float | None = None

    # MAFAULDA scoring metadata (Phase 8), additive. `severity_param` is the
    # dataset's own physical fault parameter as it appears in the directory
    # structure — grams of imbalance mass ("6g".."35g") or millimetres of shim
    # ("0.5mm".."2.0mm" horizontal, "0.51mm".."1.90mm" vertical) — and drives the
    # gradient checks (does confidence/1x amplitude rise with severity?).
    # `speed_hz` is the directory-implied NOMINAL rotation frequency (the
    # filename), carried only as the cross-check reference; the analysis itself
    # always uses the tachometer-derived rate. None for every non-MAFAULDA case.
    severity_param: str | None = None
    speed_hz: float | None = None


class AcquisitionMeta(BaseModel):
    """Analyst-declared acquisition settings from the upload form (Session
    INTAKE-HONEST). Every field is optional and PROVENANCE-ONLY: nothing in
    pdm_core or the pipeline reads this model. It exists so the report's
    Analysis-parameters table can print declared values as declared — and say
    plainly what was not provided — instead of deriving silently. The analysis
    itself always runs on the uploaded data; a declared value never overrides
    a measured one. Value validation (positivity, the window/integration
    vocabularies) happens at the form boundary (webapp/app.py, a free 422),
    so this model stays permissive and a stale stored card cannot turn into a
    sandbox parse failure.
    """

    sensor_sensitivity_mv_per_g: float | None = None
    fmax_hz: float | None = None
    spectral_lines: int | None = None
    window_type: str | None = None  # "hanning" | "flattop" | "rectangular" | "other"
    averages: int | None = None
    integration: str | None = None  # "none" | "hardware" | "software"


class Case(BaseModel):
    """A self-contained scenario bundle: everything needed to run the full
    pipeline against one machine/reading, plus the expected outcome. The
    synthetic generator (Phase 2), `vib analyze` (Phase 3), and the eval
    harness (Phase 5) all consume this same model.
    """

    name: str
    machine: MachineMeta
    sensor_data: SensorData | None = None
    # `spectra` is the ENVELOPE (bearing-detector) spectrum per axis. Session B (B1)
    # adds `raw_spectra`, the RAW/velocity spectrum per axis that the 1x-family
    # detectors (imbalance/misalignment/looseness/belt/blade-pass/resonance) read
    # instead of the envelope. Default None → those detectors fall back to the
    # bearing context (NCD path and envelope-only uploads are unchanged).
    spectra: dict[Axis, Spectrum] | None = None
    raw_spectra: dict[Axis, Spectrum] | None = None
    history: list[HistoryPoint] | None = None
    # Session HIST-1 — WHERE `history` came from, when that changes what the
    # report may claim about it. "analyst_supplied" means the points were held
    # in the analyst's own browser and posted back with the upload: we keep no
    # copy and cannot check them against the files they came from, so the trend
    # section says so. None on every other path — the NCD stream, the
    # trend-schema CSV whose points came out of the file the analyst uploaded,
    # the synthetic generator and the benchmark adapters — where the history is
    # exactly as trustworthy as the reading beside it.
    #
    # It rides on the Case deliberately. `case` is already handed to
    # report.generate.build_context() by BOTH renderers (render_markdown and
    # _html_context), so the flag reaches every document through one read; a
    # render kwarg would have had to be threaded through nine signatures,
    # worker._document_context and process_job — and process_job's parameter
    # list is pinned to process_compare_job's (Session COMPARE-FWD).
    history_source: Literal["analyst_supplied"] | None = None
    battery_percent: float | None = None
    lifecycle: LifecycleState | None = None
    # Session INTAKE-HONEST — analyst-declared acquisition settings (sensor
    # sensitivity, declared Fmax/lines, window, averages, integration).
    # Optional and provenance-only: consumed by report/charts.py::
    # analysis_parameters, never by pdm_core or the pipeline. Attached
    # centrally in adapters/uploads/__init__.py::parse_upload so every upload
    # lane carries it uniformly; None on every other path (NCD, synthetic,
    # benchmark adapters) and when the analyst declared nothing.
    acquisition: AcquisitionMeta | None = None
    # Optional so a case can be built, run through the pipeline, then have its
    # expected outcome projected from the AnalysisResult (see pipeline.analysis_to_expected).
    expected: CaseExpected | None = None

    # Provenance + eval scope (Phase 3.5 — CWRU; "upload" added Phase 6;
    # "mfpt" added Phase 7; "wind_turbine" added Phase 7B). `source`
    # distinguishes a real-data benchmark case from synthetic/eval cases and
    # webapp uploads; `validation_scope` tells the eval runner (and, for
    # uploads, the report post-processing) which assertion families are
    # meaningful for this case (CWRU and MFPT are acceleration-only: no
    # velocity, no ISO zone, no history — scope is ["rca"] only; an unscaled
    # WAV upload is likewise ["rca"]-only since amplitude has no known scale).
    #
    # "trend_relative" / "anomaly" (Phase 7B) are the acceleration-unit
    # counterparts of "trend": the wind-turbine run-to-failure data is a real
    # 50-point daily history in g, so Layer 2 z-scores and Layer 4
    # slope/R2/pct_change ARE meaningful (they are unit-relative), while the
    # velocity-based trend ALARM LIMIT and ISO zones are not. "trend_relative"
    # therefore asserts the trend's shape but not its severity gating —
    # distinct from "trend", which implies both.
    source: Literal["synthetic", "cwru", "upload", "mfpt", "wind_turbine", "mafaulda"] = "synthetic"
    validation_scope: list[
        Literal["zone", "severity", "rca", "trend", "trend_relative", "anomaly"]
    ] = Field(default_factory=lambda: ["zone", "severity", "rca", "trend"])


# ─────────────────────────────────────────────────────────────────────────
# Session HIST-2 — two-file Before/After comparison
# ─────────────────────────────────────────────────────────────────────────
#
# The output contract of pdm_core/compare.py. Every number and every verdict
# word a comparison section prints comes from one of these models, computed
# deterministically from two finished AnalysisResults — never from the drafting
# model, which is not shown the comparison at all (report/generate.py splices
# it in after drafting, the coverage-roster precedent).


#: What a comparison amounts to. `not_comparable` and `gate_blocked` are
#: RESULTS, not exceptions: the first says these two files are not Before/After
#: of one measurement point, the second says one of them did not pass its own
#: data-quality gate. Neither ever degrades into a comparison with a caveat.
ComparisonStatus = Literal["ok", "gate_blocked", "not_comparable"]

#: The honest delta vocabulary, closed on purpose. A comparison may say a level
#: worsened, improved, or did not significantly change — or that the question
#: could not be answered from what was measured. There is no fifth word.
DeltaVerdict = Literal["worsened", "improved", "no_significant_change", "not_assessable"]

#: Repair verification speaks in its own register. "consistent with repair" is
#: the strongest claim the evidence can support — a spectrum cannot witness a
#: repair, only the absence of the signature that justified one. The word
#: "repaired" is deliberately absent from this vocabulary.
RepairVerdict = Literal["consistent_with_repair", "not_verified", "not_applicable"]


class OverallChange(BaseModel):
    """The overall-level movement between the two readings, in mm/s.

    Requires an ASSESSABLE ISO zone on both sides: `mark_not_assessable`
    preserves `severity_rms` while withholding the zone (implausible units), and
    an acceleration-only reading has no velocity at all. Comparing either would
    be arithmetic on a number the product has already refused to interpret, so
    the verdict is `not_assessable` and `reason` says which side and why.
    """

    before_mms: float | None = None
    after_mms: float | None = None
    delta_mms: float | None = None
    pct_change: float | None = None
    zone_before: IsoZoneOrNA | None = None
    zone_after: IsoZoneOrNA | None = None
    zone_movement: Literal["up", "down", "same"] | None = None
    verdict: DeltaVerdict = "not_assessable"
    reason: str | None = None
    #: Stated whenever the ISO zone moved but the change did not clear the
    #: significance ratio — a boundary crossed by measurement noise is a fact
    #: worth printing and NOT a verdict worth drawing.
    zone_note: str | None = None


class BandChange(BaseModel):
    """One shaft-order band's level in both readings.

    `before`/`after` are band LEVELS (root-sum-square of the bin amplitudes in
    the band). They are never interpreted absolutely — only their ratio is, and
    a ratio is unit-free, which is what makes the band comparison meaningful on
    acceleration-only data where no ISO severity exists.
    """

    name: str
    label: str
    lo_order: float
    #: The order span the band ACTUALLY covers, which is its definition clipped
    #: to the common Fmax — never the definition itself, or the order range and
    #: the frequency range printed beside it would disagree.
    hi_order: float | None
    lo_hz: float
    hi_hz: float
    before: float
    after: float
    delta: float
    pct_change: float | None = None
    verdict: DeltaVerdict = "not_assessable"


class PeakChange(BaseModel):
    """One peak, seen in one or both readings.

    `state` is the FACT: the peak was matched across both readings, present only
    before ("gone"), or present only after ("new").

    `verdict` is set for MATCHED peaks only, and is a statement about that
    tone's amplitude — the same ratio rule the bands use — never a diagnosis. A
    "gone" or "new" peak carries no verdict at all: a tone disappearing is not
    by itself an improvement of the machine, and saying so would be the kind of
    claim this product refuses.
    """

    state: Literal["matched", "gone", "new"]
    before_hz: float | None = None
    after_hz: float | None = None
    before_amp: float | None = None
    after_amp: float | None = None
    order: float | None = None
    pct_change: float | None = None
    verdict: DeltaVerdict = "not_assessable"


class SpectrumComparison(BaseModel):
    """The band-by-band and peak-by-peak comparison of ONE (kind, axis) pair.

    Both spectra are truncated to their COMMON Fmax before anything is measured,
    so every band and every peak universe covers exactly the same frequency
    span on both sides; `fmax_hz` is that common span and `truncated` says
    whether either side lost content to it.
    """

    kind: SpectrumKind
    axis: Axis
    shaft_hz: float
    fmax_hz: float
    bin_hz: float
    truncated: bool = False
    truncation_note: str | None = None
    bands: list[BandChange] = Field(default_factory=list)
    matched: list[PeakChange] = Field(default_factory=list)
    new_frequencies: list[PeakChange] = Field(default_factory=list)
    gone_frequencies: list[PeakChange] = Field(default_factory=list)


class RepairCheck(BaseModel):
    """One fault the BEFORE analysis committed to, re-examined in the AFTER
    reading. `after_committed` is the decisive input: if the After analysis
    committed the same fault on its own evidence, no amplitude arithmetic can
    make the repair verified.
    """

    fault: str
    description: str
    freq_hz: float | None = None
    before_amp: float | None = None
    after_amp: float | None = None
    pct_change: float | None = None
    after_committed: bool = False
    verdict: RepairVerdict = "not_verified"
    evidence: str = ""


class ComparisonResult(BaseModel):
    """The full output of pdm_core.compare.compare_readings().

    `status` gates everything: on `not_comparable` the caller must fail the job
    (the ratified `not_comparable` code) and nothing below is populated; on
    `gate_blocked` the ordinary insufficient-data report is the answer and the
    comparison section states which reading failed and what to re-capture.
    """

    status: ComparisonStatus
    reason: str | None = None
    reason_code: str | None = None

    before_ts: str | None = None
    after_ts: str | None = None

    overall: OverallChange | None = None
    spectra: list[SpectrumComparison] = Field(default_factory=list)
    spectra_note: str | None = None

    repair: list[RepairCheck] = Field(default_factory=list)
    repair_verdict: RepairVerdict = "not_applicable"

    #: The headline delta verdict — the overall-level movement when it is
    #: assessable, otherwise the aggregate of the band verdicts.
    verdict: DeltaVerdict = "not_assessable"
    headline: str = ""
    #: What would resolve whatever this comparison could not answer. The same
    #: promise the recommendations engine makes, kept here because the reason a
    #: comparison falls short is a property of the PAIR, not of either reading.
    what_to_collect: list[str] = Field(default_factory=list)
    #: The significance rule this comparison applied, in words, so a reader can
    #: apply their own bar to the printed percentages.
    significance_note: str = ""
